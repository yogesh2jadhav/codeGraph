package com.demo.svc;

import com.demo.repo.OrderRepository;
import org.springframework.stereotype.Service;

@Service
public class OrderService {

    private final OrderRepository repository;

    public OrderService(OrderRepository repository) {
        this.repository = repository;
    }

    public Order findOrder(String id) {
        return repository.findById(id);
    }

    public Order place(Order order) {
        return repository.save(order);
    }
}
